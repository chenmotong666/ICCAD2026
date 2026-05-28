module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n12,
    n30,
    n20,
    n22,
    n21,
    n23
);

  input n0;
  input n1;
  input n2;
  input n3;
  input n4;
  input n5;
  input n6;
  input n7;
  input n8;
  input [3:0] n12;
  output [1:0] n30;
  output n20;
  output n22;
  output n21;
  output n23;

  wire \$and$testcase/test52/test52.v:11$4_Y , \$or$testcase/test52/test52.v:12$6_Y , \$xor$testcase/test52/test52.v:13$8_Y , n100, n101, n102, n103, n104, n105, n107, n108, n109, n110, n111, n114, n120, n121, n122, n123, n124, n125, n126, n127, n128, n129, n130, n131, n140, n141, n142;

  and   \$and$testcase/test52/test52.v:15$10  (n107, n2, n3);
  and   \$and$testcase/test52/test52.v:18$13  (n109, n2, n3);
  and   \$and$testcase/test52/test52.v:19$14  (n110, n2, n3);
  and   \$and$testcase/test52/test52.v:29$18  (n120, n2, n3);
  and   \$and$testcase/test52/test52.v:35$24  (n126, n2, n3);
  and   \$and$testcase/test52/test52.v:7$1  (n100, n2, n3);
  and   \$and$testcase/test52/test52.v:30$19  (n121, n2, n4);
  and   \$and$testcase/test52/test52.v:36$25  (n127, n2, n4);
  not   \$not$testcase/test52/test52.v:20$35  (n111, n4);
  not   \$not$testcase/test52/test52.v:23$37  (n114, n4);
  and   \$and$testcase/test52/test52.v:31$20  (n122, n2, n5);
  and   \$and$testcase/test52/test52.v:32$21  (n123, n2, n6);
  and   \$and$testcase/test52/test52.v:33$22  (n124, n2, n7);
  and   \$and$testcase/test52/test52.v:39$28  (n140, n6, n7);
  and   \$and$testcase/test52/test52.v:34$23  (n125, n2, n8);
  xor   \$xor$testcase/test52/test52.v:44$31  (n131, n12[0], n12[1]);
  or    \$or$testcase/test52/test52.v:46$33  (n30[1], n12[2], n12[3]);
  or    \$or$testcase/test52/test52.v:16$11  (n108, n107, n5);
  or    \$or$testcase/test52/test52.v:8$2  (n101, n100, n4);
  or    \$or$testcase/test52/test52.v:37$26  (n128, n120, n121);
  or    \$or$testcase/test52/test52.v:25$15  (n22, n114, n100);
  xor   \$xor$testcase/test52/test52.v:38$27  (n129, n122, n123);
  or    \$or$testcase/test52/test52.v:40$29  (n141, n140, n8);
  xor   \$xor$testcase/test52/test52.v:17$12  (n21, n108, n6);
  not   \$not$testcase/test52/test52.v:9$34  (n102, n101);
  and   \$and$testcase/test52/test52.v:43$30  (n130, n129, n128);
  not   \$not$testcase/test52/test52.v:41$38  (n142, n141);
  xor   \$xor$testcase/test52/test52.v:10$3  (n103, n102, n5);
  and   \$and$testcase/test52/test52.v:45$32  (n30[0], n131, n130);
  and   \$and$testcase/test52/test52.v:11$4  (\$and$testcase/test52/test52.v:11$4_Y , n103, n6);
  not   \$not$testcase/test52/test52.v:11$5  (n104, \$and$testcase/test52/test52.v:11$4_Y );
  or    \$or$testcase/test52/test52.v:12$6  (\$or$testcase/test52/test52.v:12$6_Y , n104, n7);
  not   \$not$testcase/test52/test52.v:12$7  (n105, \$or$testcase/test52/test52.v:12$6_Y );
  xor   \$xor$testcase/test52/test52.v:13$8  (\$xor$testcase/test52/test52.v:13$8_Y , n105, n8);
  not   \$not$testcase/test52/test52.v:13$9  (n20, \$xor$testcase/test52/test52.v:13$8_Y );

  assign n23 = 1'b1;

endmodule
