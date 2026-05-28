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
  output n20;
  output n22;
  output n21;
  output n23;

  wire \$and$testcase/test69/test69.v:9$4_Y , \$auto$rtlil.cc:3255:Not$34 , \$or$testcase/test69/test69.v:10$6_Y , \$xor$testcase/test69/test69.v:11$8_Y , n100, n101, n102, n103, n104, n105, n107, n108, n109, n110, n111, n114, n120, n121, n122, n123, n128, n129, n130, n140, n141, n142;

  and   \$and$testcase/test69/test69.v:13$10  (n107, n2, n3);
  and   \$and$testcase/test69/test69.v:16$13  (n109, n2, n3);
  and   \$and$testcase/test69/test69.v:17$14  (n110, n2, n3);
  and   \$and$testcase/test69/test69.v:27$19  (n120, n2, n3);
  and   \$and$testcase/test69/test69.v:5$1  (n100, n2, n3);
  and   \$and$testcase/test69/test69.v:28$20  (n121, n2, n4);
  not   \$not$testcase/test69/test69.v:18$29  (n111, n4);
  not   \$not$testcase/test69/test69.v:21$31  (n114, n4);
  and   \$and$testcase/test69/test69.v:29$21  (n122, n2, n5);
  not   \$auto$opt_expr.cc:605:replace_const_cells$33  (\$auto$rtlil.cc:3255:Not$34 , n5);
  and   \$and$testcase/test69/test69.v:30$22  (n123, n2, n6);
  and   \$and$testcase/test69/test69.v:33$25  (n140, n6, n7);
  or    \$or$testcase/test69/test69.v:14$11  (n108, n107, n5);
  or    \$or$testcase/test69/test69.v:6$2  (n101, n100, n4);
  or    \$or$testcase/test69/test69.v:31$23  (n128, n120, n121);
  or    \$or$testcase/test69/test69.v:23$15  (n22, n114, n100);
  xor   \$xor$testcase/test69/test69.v:32$24  (n129, n122, n123);
  or    \$or$testcase/test69/test69.v:34$26  (n141, n140, n8);
  xor   \$xor$testcase/test69/test69.v:15$12  (n21, n108, n6);
  not   \$not$testcase/test69/test69.v:7$28  (n102, n101);
  and   \$and$testcase/test69/test69.v:37$27  (n130, n129, n128);
  not   \$not$testcase/test69/test69.v:35$32  (n142, n141);
  xor   \$xor$testcase/test69/test69.v:8$3  (n103, n102, n5);
  and   \$and$testcase/test69/test69.v:9$4  (\$and$testcase/test69/test69.v:9$4_Y , n103, n6);
  not   \$not$testcase/test69/test69.v:9$5  (n104, \$and$testcase/test69/test69.v:9$4_Y );
  or    \$or$testcase/test69/test69.v:10$6  (\$or$testcase/test69/test69.v:10$6_Y , n104, n7);
  not   \$not$testcase/test69/test69.v:10$7  (n105, \$or$testcase/test69/test69.v:10$6_Y );
  xor   \$xor$testcase/test69/test69.v:11$8  (\$xor$testcase/test69/test69.v:11$8_Y , n105, n8);
  not   \$not$testcase/test69/test69.v:11$9  (n20, \$xor$testcase/test69/test69.v:11$8_Y );

  assign n23 = n5;

endmodule
