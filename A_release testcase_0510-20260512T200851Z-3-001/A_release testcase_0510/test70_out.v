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
    n31,
    n32,
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
  output n31;
  output n32;
  output n23;

  wire \$and$testcase/test70/test70.v:12$4_Y , \$or$testcase/test70/test70.v:13$6_Y , \$xor$testcase/test70/test70.v:14$8_Y , n100, n101, n102, n103, n104, n105, n107, n108, n114, n120, n121, n122, n123, n128, n129, n130, n131;

  and   \$and$testcase/test70/test70.v:16$10  (n107, n2, n3);
  and   \$and$testcase/test70/test70.v:30$18  (n120, n2, n3);
  and   \$and$testcase/test70/test70.v:8$1  (n100, n2, n3);
  and   \$and$testcase/test70/test70.v:31$19  (n121, n2, n4);
  not   \$not$testcase/test70/test70.v:24$37  (n114, n4);
  and   \$and$testcase/test70/test70.v:32$20  (n122, n2, n5);
  and   \$and$testcase/test70/test70.v:33$21  (n123, n2, n6);
  xor   \$xor$testcase/test70/test70.v:45$31  (n131, n12[0], n12[1]);
  or    \$or$testcase/test70/test70.v:47$33  (n30[1], n12[2], n12[3]);
  or    \$or$testcase/test70/test70.v:17$11  (n108, n107, n5);
  or    \$or$testcase/test70/test70.v:9$2  (n101, n100, n4);
  or    \$or$testcase/test70/test70.v:38$26  (n128, n120, n121);
  or    \$or$testcase/test70/test70.v:26$15  (n22, n114, n100);
  xor   \$xor$testcase/test70/test70.v:39$27  (n129, n122, n123);
  xor   \$xor$testcase/test70/test70.v:18$12  (n21, n108, n6);
  not   \$not$testcase/test70/test70.v:10$34  (n102, n101);
  and   \$and$testcase/test70/test70.v:44$30  (n130, n129, n128);
  xor   \$xor$testcase/test70/test70.v:11$3  (n103, n102, n5);
  and   \$and$testcase/test70/test70.v:46$32  (n30[0], n131, n130);
  dff g72 (.Q(n31), .RN(n1), .SN(1'b1), .CK(n0), .D(n130));
  and   \$and$testcase/test70/test70.v:12$4  (\$and$testcase/test70/test70.v:12$4_Y , n103, n6);
  not   \$not$testcase/test70/test70.v:12$5  (n104, \$and$testcase/test70/test70.v:12$4_Y );
  or    \$or$testcase/test70/test70.v:13$6  (\$or$testcase/test70/test70.v:13$6_Y , n104, n7);
  not   \$not$testcase/test70/test70.v:13$7  (n105, \$or$testcase/test70/test70.v:13$6_Y );
  xor   \$xor$testcase/test70/test70.v:14$8  (\$xor$testcase/test70/test70.v:14$8_Y , n105, n8);
  not   \$not$testcase/test70/test70.v:14$9  (n20, \$xor$testcase/test70/test70.v:14$8_Y );
  dff g73 (.Q(n32), .RN(n1), .SN(1'b1), .CK(n0), .D(n20));

  assign n23 = 1'b1;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
